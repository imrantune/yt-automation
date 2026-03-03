"""FastAPI web dashboard for Spartacus automation pipeline."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select

from config.settings import get_settings
from database.connection import SessionLocal
from database.models import (
    ApiCostLog,
    Character,
    CharacterStat,
    Episode,
    EpisodeSEO,
    EpisodeStatus,
    JobLog,
    JobStatus,
    Scene,
    Short,
    StepStatus,
    VideoJob,
)

PIPELINE_STEPS = [
    {"key": "scripting", "label": "Script"},
    {"key": "seo", "label": "SEO"},
    {"key": "voiceover", "label": "Voiceover"},
    {"key": "subtitles", "label": "Subtitles"},
    {"key": "image_gen", "label": "DALL-E"},
    {"key": "ken_burns", "label": "Ken Burns"},
    {"key": "video_gen", "label": "Video Gen"},
    {"key": "music", "label": "Music"},
    {"key": "sfx", "label": "SFX"},
    {"key": "editing", "label": "Merge"},
    {"key": "thumbnail", "label": "Thumbnail"},
    {"key": "shorts", "label": "Shorts"},
    {"key": "upload", "label": "Upload"},
]

settings = get_settings(require_api_keys=False)

WEB_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = settings.output_root.resolve()

# Prevent concurrent pipeline operations on the same episode
_episode_locks: dict[int, str] = {}
_episode_locks_mu = threading.Lock()

app = FastAPI(title="Spartacus Arena Dashboard", docs_url="/docs")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(OUTPUT_DIR)), name="media")
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def _file_to_media_url(file_path: str | None) -> str | None:
    """Convert an absolute/relative output path to a /media/ URL."""
    if not file_path:
        return None
    p = Path(file_path).resolve()
    try:
        rel = p.relative_to(OUTPUT_DIR)
        return f"/media/{rel.as_posix()}"
    except ValueError:
        return None


def _db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ─── Dashboard ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    session = SessionLocal()
    try:
        total_episodes = session.execute(select(func.count()).select_from(Episode)).scalar_one()
        ready_episodes = session.execute(
            select(func.count()).select_from(Episode).where(Episode.status == EpisodeStatus.READY)
        ).scalar_one()
        failed_episodes = session.execute(
            select(func.count()).select_from(Episode).where(Episode.status == EpisodeStatus.FAILED)
        ).scalar_one()
        total_characters = session.execute(select(func.count()).select_from(Character)).scalar_one()
        alive_characters = session.execute(
            select(func.count()).select_from(Character).where(Character.is_alive.is_(True))
        ).scalar_one()
        total_jobs = session.execute(select(func.count()).select_from(VideoJob)).scalar_one()
        running_jobs = session.execute(
            select(func.count()).select_from(VideoJob).where(VideoJob.status == JobStatus.RUNNING)
        ).scalar_one()

        recent_episodes = list(session.execute(
            select(Episode).order_by(desc(Episode.created_at)).limit(5)
        ).scalars())
        recent_jobs = list(session.execute(
            select(VideoJob).order_by(desc(VideoJob.created_at)).limit(5)
        ).scalars())

        total_cost = session.query(func.sum(ApiCostLog.cost_usd)).scalar() or 0.0

        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "total_episodes": total_episodes,
            "ready_episodes": ready_episodes,
            "failed_episodes": failed_episodes,
            "total_characters": total_characters,
            "alive_characters": alive_characters,
            "total_jobs": total_jobs,
            "running_jobs": running_jobs,
            "recent_episodes": recent_episodes,
            "recent_jobs": recent_jobs,
            "total_cost_usd": round(float(total_cost), 4),
        })
    finally:
        session.close()


# ─── Episodes ────────────────────────────────────────────────────────────────

@app.get("/episodes", response_class=HTMLResponse)
async def episodes_list(request: Request):
    session = SessionLocal()
    try:
        episodes = list(session.execute(
            select(Episode).order_by(desc(Episode.created_at)).limit(50)
        ).scalars())
        return templates.TemplateResponse("episodes.html", {
            "request": request,
            "episodes": episodes,
        })
    finally:
        session.close()


@app.get("/episodes/{episode_id}", response_class=HTMLResponse)
async def episode_detail(request: Request, episode_id: int):
    session = SessionLocal()
    try:
        episode = session.execute(
            select(Episode).where(Episode.id == episode_id)
        ).scalar_one_or_none()
        if not episode:
            return HTMLResponse("<h1>Episode not found</h1>", status_code=404)

        scenes = list(session.execute(
            select(Scene).where(Scene.episode_id == episode_id).order_by(Scene.scene_order.asc())
        ).scalars())
        stats = list(session.execute(
            select(CharacterStat).where(CharacterStat.episode_id == episode_id)
        ).scalars())
        seo = session.execute(
            select(EpisodeSEO).where(EpisodeSEO.episode_id == episode_id)
        ).scalar_one_or_none()
        shorts = list(session.execute(
            select(Short).where(Short.episode_id == episode_id)
        ).scalars())
        jobs = list(session.execute(
            select(VideoJob).where(VideoJob.episode_id == episode_id)
        ).scalars())

        for stat in stats:
            stat.character_name = session.execute(
                select(Character.name).where(Character.id == stat.character_id)
            ).scalar_one_or_none() or "Unknown"

        video_job = None
        for j in jobs:
            if j.final_video_path:
                video_job = j
                break

        final_video_url = _file_to_media_url(video_job.final_video_path) if video_job else None
        thumbnail_url = _file_to_media_url(video_job.thumbnail_path) if video_job else None

        scene_media = []
        for scene in scenes:
            img_path = settings.output_root / "images" / f"episode_{episode.episode_number:04d}_scene_{scene.scene_order:02d}.png"
            scene_media.append({
                "scene": scene,
                "audio_url": _file_to_media_url(scene.audio_file_path),
                "video_url": _file_to_media_url(scene.video_clip_path),
                "subtitle_url": _file_to_media_url(scene.subtitle_file_path),
                "image_url": _file_to_media_url(str(img_path)) if img_path.exists() else None,
            })

        short_media = []
        for short in shorts:
            short_media.append({
                "short": short,
                "video_url": _file_to_media_url(short.file_path),
            })

        from pipeline.cost_tracker import get_episode_costs
        cost_data = get_episode_costs(session, episode_id)

        return templates.TemplateResponse("episode_detail.html", {
            "request": request,
            "episode": episode,
            "scenes": scenes,
            "scene_media": scene_media,
            "stats": stats,
            "seo": seo,
            "shorts": shorts,
            "short_media": short_media,
            "jobs": jobs,
            "final_video_url": final_video_url,
            "thumbnail_url": thumbnail_url,
            "cost_data": cost_data,
        })
    finally:
        session.close()


# ─── Jobs ────────────────────────────────────────────────────────────────────

@app.get("/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request):
    session = SessionLocal()
    try:
        jobs = list(session.execute(
            select(VideoJob).order_by(desc(VideoJob.created_at)).limit(50)
        ).scalars())
        return templates.TemplateResponse("jobs.html", {
            "request": request,
            "jobs": jobs,
        })
    finally:
        session.close()


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: int):
    session = SessionLocal()
    try:
        job = session.execute(
            select(VideoJob).where(VideoJob.id == job_id)
        ).scalar_one_or_none()
        if not job:
            return HTMLResponse("<h1>Job not found</h1>", status_code=404)

        logs = list(session.execute(
            select(JobLog).where(JobLog.job_id == job_id).order_by(JobLog.logged_at.asc())
        ).scalars())

        final_video_url = _file_to_media_url(job.final_video_path)
        thumbnail_url = _file_to_media_url(job.thumbnail_path)

        return templates.TemplateResponse("job_detail.html", {
            "request": request,
            "job": job,
            "logs": logs,
            "final_video_url": final_video_url,
            "thumbnail_url": thumbnail_url,
        })
    finally:
        session.close()


# ─── Characters ──────────────────────────────────────────────────────────────

@app.get("/characters", response_class=HTMLResponse)
async def characters_list(request: Request):
    session = SessionLocal()
    try:
        characters = list(session.execute(
            select(Character).order_by(Character.name.asc())
        ).scalars())
        return templates.TemplateResponse("characters.html", {
            "request": request,
            "characters": characters,
        })
    finally:
        session.close()


# ─── Generate ────────────────────────────────────────────────────────────────

@app.get("/generate", response_class=HTMLResponse)
async def generate_page(request: Request):
    return templates.TemplateResponse("generate.html", {"request": request})


@app.post("/api/generate")
async def api_generate():
    """Trigger pipeline in background thread, return job tracking info."""
    def _run():
        try:
            from main import run_pipeline
            run_pipeline()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("Background pipeline failed: %s", exc)
            try:
                from database.connection import SessionLocal as SL, log_job_step
                from database.models import StepStatus, JobStatus
                from sqlalchemy import desc
                s = SL()
                job = s.execute(select(VideoJob).order_by(desc(VideoJob.id)).limit(1)).scalar_one_or_none()
                if job and job.status == JobStatus.RUNNING:
                    job.status = JobStatus.FAILED
                    s.add(job)
                    log_job_step(s, job.id, "pipeline", StepStatus.FAILED, f"Pipeline crashed: {str(exc)[:500]}")
                    s.commit()
                s.close()
            except Exception:
                pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return JSONResponse({"status": "started", "message": "Pipeline started in background."})


@app.get("/api/jobs/{job_id}/logs")
async def api_job_logs(job_id: int):
    """JSON endpoint for polling job logs."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(VideoJob).where(VideoJob.id == job_id)
        ).scalar_one_or_none()
        logs = list(session.execute(
            select(JobLog).where(JobLog.job_id == job_id).order_by(JobLog.logged_at.asc())
        ).scalars())
        return JSONResponse({
            "job_status": job.status.value if job else "unknown",
            "logs": [
                {
                    "step": log.step,
                    "status": log.status.value,
                    "message": log.message,
                    "logged_at": log.logged_at.isoformat() if log.logged_at else None,
                }
                for log in logs
            ],
        })
    finally:
        session.close()


@app.get("/api/jobs/latest")
async def api_latest_job():
    """Get the latest job ID for polling."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(VideoJob).order_by(desc(VideoJob.id)).limit(1)
        ).scalar_one_or_none()
        return JSONResponse({"job_id": job.id if job else None, "status": job.status.value if job else None})
    finally:
        session.close()


# ─── Active Pipeline Tracking ─────────────────────────────────────────────────

def _build_step_timeline(logs: list[JobLog]) -> list[dict]:
    """Build per-step timing from log entries.

    Returns a list of dicts: {key, label, status, started_at, completed_at, duration_seconds}.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    step_data: dict[str, dict] = {}
    for log in logs:
        key = log.step
        if key == "video_gen_scene" or key == "pipeline":
            continue
        if key not in step_data:
            step_data[key] = {"started_at": None, "completed_at": None, "status": None}
        if log.status == StepStatus.STARTED:
            step_data[key]["started_at"] = log.logged_at
            if step_data[key]["status"] is None:
                step_data[key]["status"] = "running"
        elif log.status == StepStatus.SUCCESS:
            step_data[key]["completed_at"] = log.logged_at
            step_data[key]["status"] = "success"
        elif log.status == StepStatus.FAILED:
            step_data[key]["completed_at"] = log.logged_at
            step_data[key]["status"] = "failed"

    timeline = []
    for step_def in PIPELINE_STEPS:
        key = step_def["key"]
        info = step_data.get(key)
        if info:
            started = info["started_at"]
            completed = info["completed_at"]
            duration = None
            if started and completed:
                duration = round((completed - started).total_seconds(), 1)
            elif started:
                duration = round((now - started).total_seconds(), 1)
            timeline.append({
                "key": key,
                "label": step_def["label"],
                "status": info["status"] or "pending",
                "started_at": started.isoformat() if started else None,
                "completed_at": completed.isoformat() if completed else None,
                "duration_seconds": duration,
            })
        else:
            timeline.append({
                "key": key,
                "label": step_def["label"],
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "duration_seconds": None,
            })
    return timeline


@app.get("/api/pipelines/active")
async def api_pipelines_active():
    """Return all running/pending jobs with step-by-step timeline and timing."""
    session = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        active_jobs = list(session.execute(
            select(VideoJob).where(
                VideoJob.status.in_([JobStatus.RUNNING, JobStatus.PENDING])
            ).order_by(desc(VideoJob.created_at))
        ).scalars())

        recent_done = list(session.execute(
            select(VideoJob).where(
                VideoJob.status.in_([JobStatus.READY, JobStatus.FAILED])
            ).order_by(desc(VideoJob.created_at)).limit(3)
        ).scalars())

        result = []
        for job in active_jobs + recent_done:
            logs = list(session.execute(
                select(JobLog).where(JobLog.job_id == job.id).order_by(JobLog.logged_at.asc())
            ).scalars())
            timeline = _build_step_timeline(logs)
            current_step = None
            for step in timeline:
                if step["status"] == "running":
                    current_step = step["label"]
                    break

            elapsed = None
            if job.created_at:
                if job.completed_at:
                    elapsed = round((job.completed_at - job.created_at).total_seconds(), 1)
                else:
                    elapsed = round((now - job.created_at).total_seconds(), 1)

            ep_title = None
            if job.episode:
                ep_title = job.episode.title

            result.append({
                "job_id": job.id,
                "episode_id": job.episode_id,
                "episode_title": ep_title,
                "status": job.status.value,
                "current_step": current_step,
                "elapsed_seconds": elapsed,
                "timeline": timeline,
            })

        return JSONResponse({"pipelines": result})
    finally:
        session.close()


@app.get("/api/jobs/{job_id}/timeline")
async def api_job_timeline(job_id: int):
    """Return step-by-step timeline with durations for a specific job."""
    session = SessionLocal()
    try:
        job = session.execute(
            select(VideoJob).where(VideoJob.id == job_id)
        ).scalar_one_or_none()
        if not job:
            return JSONResponse({"error": "Job not found"}, status_code=404)

        logs = list(session.execute(
            select(JobLog).where(JobLog.job_id == job_id).order_by(JobLog.logged_at.asc())
        ).scalars())
        timeline = _build_step_timeline(logs)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        elapsed = None
        if job.created_at:
            if job.completed_at:
                elapsed = round((job.completed_at - job.created_at).total_seconds(), 1)
            else:
                elapsed = round((now - job.created_at).total_seconds(), 1)

        return JSONResponse({
            "job_id": job.id,
            "status": job.status.value,
            "elapsed_seconds": elapsed,
            "timeline": timeline,
        })
    finally:
        session.close()


# ─── Wan 2.1 Model Status ─────────────────────────────────────────────────────

def _get_wan21_model_status() -> dict:
    """Scan HuggingFace cache to report Wan 2.1 download progress."""
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    model_dir = hf_home / "hub" / "models--Wan-AI--Wan2.1-T2V-1.3B-Diffusers"
    blobs_dir = model_dir / "blobs"

    if not model_dir.exists():
        return {
            "installed": False,
            "downloading": False,
            "model_id": settings.wan21_model_id,
            "enabled": settings.wan21_enabled,
            "total_files": 0,
            "complete_files": 0,
            "incomplete_files": 0,
            "downloaded_bytes": 0,
            "downloaded_gb": 0.0,
            "expected_total_gb": 22.0,
            "progress_pct": 0.0,
            "files": [],
        }

    complete_files = []
    incomplete_files = []
    total_downloaded = 0

    if blobs_dir.exists():
        for entry in blobs_dir.iterdir():
            if not entry.is_file():
                continue
            size = entry.stat().st_size
            total_downloaded += size
            name = entry.name
            if name.endswith(".incomplete"):
                incomplete_files.append({"name": name.replace(".incomplete", "")[:12] + "...", "bytes": size, "status": "downloading"})
            else:
                complete_files.append({"name": name[:12] + "...", "bytes": size, "status": "complete"})

    total_files = len(complete_files) + len(incomplete_files)
    downloaded_gb = round(total_downloaded / (1024 ** 3), 2)
    expected_gb = 22.0
    progress = min(100.0, round((total_downloaded / (expected_gb * 1024 ** 3)) * 100, 1))
    is_installed = len(incomplete_files) == 0 and len(complete_files) >= 15
    is_downloading = len(incomplete_files) > 0

    big_files = sorted(
        incomplete_files + [f for f in complete_files if f["bytes"] > 10_000_000],
        key=lambda x: x["bytes"],
        reverse=True,
    )

    return {
        "installed": is_installed,
        "downloading": is_downloading,
        "model_id": settings.wan21_model_id,
        "enabled": settings.wan21_enabled,
        "total_files": total_files,
        "complete_files": len(complete_files),
        "incomplete_files": len(incomplete_files),
        "downloaded_bytes": total_downloaded,
        "downloaded_gb": downloaded_gb,
        "expected_total_gb": expected_gb,
        "progress_pct": progress,
        "files": big_files[:10],
    }


@app.get("/api/wan21/status")
async def api_wan21_status():
    """Return Wan 2.1 model download/install status."""
    data = _get_wan21_model_status()
    data["download_active"] = _wan21_download_active
    data["verified"] = _wan21_verified
    data["verify_error"] = _wan21_verify_error
    data["verify_active"] = _wan21_verify_active
    return JSONResponse(data)


_wan21_download_active = False
_wan21_download_error: str | None = None
_wan21_verified: bool | None = None
_wan21_verify_error: str | None = None
_wan21_verify_active = False


@app.post("/api/wan21/download")
async def api_wan21_download():
    """Start Wan 2.1 model download in background thread."""
    global _wan21_download_active, _wan21_download_error
    if _wan21_download_active:
        return JSONResponse({"status": "already_running", "message": "Download already in progress."})

    status = _get_wan21_model_status()
    if status["installed"]:
        return JSONResponse({"status": "installed", "message": "Model already installed."})

    _wan21_download_active = True
    _wan21_download_error = None

    def _download():
        global _wan21_download_active, _wan21_download_error
        import logging
        log = logging.getLogger("wan21_download")
        try:
            hf_token = os.environ.get("HF_TOKEN", "").strip() or None
            model_id = settings.wan21_model_id
            log.info("Starting Wan 2.1 download: %s (token=%s)", model_id, "yes" if hf_token else "no")

            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id=model_id,
                token=hf_token,
                resume_download=True,
                max_workers=2,
            )
            log.info("Wan 2.1 download complete.")
        except Exception as exc:
            _wan21_download_error = str(exc)
            log.exception("Wan 2.1 download failed: %s", exc)
        finally:
            _wan21_download_active = False

    thread = threading.Thread(target=_download, daemon=True)
    thread.start()
    return JSONResponse({"status": "started", "message": "Wan 2.1 download started in background."})


@app.post("/api/wan21/verify")
async def api_wan21_verify():
    """Load Wan 2.1 model to verify installation. Runs in background."""
    global _wan21_verify_active, _wan21_verified, _wan21_verify_error
    if _wan21_verify_active:
        return JSONResponse({"status": "already_running", "message": "Verification already in progress."})

    status = _get_wan21_model_status()
    if not status["installed"]:
        return JSONResponse({"error": "Model not installed yet."}, status_code=400)

    _wan21_verify_active = True
    _wan21_verified = None
    _wan21_verify_error = None

    def _verify():
        global _wan21_verify_active, _wan21_verified, _wan21_verify_error
        import logging
        log = logging.getLogger("wan21_verify")
        try:
            log.info("Verifying Wan 2.1 model load...")
            import torch
            from diffusers import DiffusionPipeline

            device = settings.wan21_device
            dtype = torch.float16 if device in {"cuda", "mps"} else torch.float32
            log.info("Loading pipeline: %s (device=%s, dtype=%s)", settings.wan21_model_id, device, dtype)
            pipe = DiffusionPipeline.from_pretrained(settings.wan21_model_id, torch_dtype=dtype)
            pipe = pipe.to(device)

            log.info("Model loaded. Running quick inference test (4 frames)...")
            result = pipe(
                prompt="A golden sunset over an ancient Roman colosseum",
                num_frames=4,
                num_inference_steps=5,
                guidance_scale=7.0,
            )
            frames = result.frames[0] if isinstance(result.frames, list) else result.frames
            if frames is not None and len(frames) > 0:
                _wan21_verified = True
                log.info("Wan 2.1 verification PASSED — model loads and generates frames.")
            else:
                _wan21_verified = False
                _wan21_verify_error = "Model loaded but produced no frames."
                log.warning("Wan 2.1 verification FAILED — no frames produced.")

            del pipe
            if device == "mps":
                torch.mps.empty_cache()
            elif device == "cuda":
                torch.cuda.empty_cache()

        except Exception as exc:
            _wan21_verified = False
            _wan21_verify_error = str(exc)[:500]
            log.exception("Wan 2.1 verification FAILED: %s", exc)
        finally:
            _wan21_verify_active = False

    thread = threading.Thread(target=_verify, daemon=True)
    thread.start()
    return JSONResponse({"status": "started", "message": "Model verification started — loading pipeline..."})


# ─── Cost Tracking ────────────────────────────────────────────────────────────

@app.get("/api/episodes/{episode_id}/costs")
async def api_episode_costs(episode_id: int):
    """Return detailed cost breakdown for an episode."""
    session = SessionLocal()
    try:
        from pipeline.cost_tracker import get_episode_costs
        return JSONResponse(get_episode_costs(session, episode_id))
    finally:
        session.close()


@app.get("/api/costs/summary")
async def api_costs_summary():
    """Return total costs across all episodes."""
    session = SessionLocal()
    try:
        from sqlalchemy import func
        rows = session.query(
            ApiCostLog.service,
            func.sum(ApiCostLog.cost_usd).label("total"),
            func.count(ApiCostLog.id).label("calls"),
            func.sum(ApiCostLog.input_units).label("total_input"),
        ).group_by(ApiCostLog.service).all()

        grand_total = 0.0
        services = []
        for row in rows:
            total = round(float(row.total or 0), 4)
            grand_total += total
            services.append({
                "service": row.service,
                "total_usd": total,
                "api_calls": int(row.calls or 0),
                "total_input_units": int(row.total_input or 0),
            })

        ep_costs = session.query(
            ApiCostLog.episode_id,
            func.sum(ApiCostLog.cost_usd).label("total"),
        ).group_by(ApiCostLog.episode_id).order_by(ApiCostLog.episode_id.desc()).limit(20).all()

        return JSONResponse({
            "grand_total_usd": round(grand_total, 4),
            "by_service": services,
            "recent_episodes": [
                {"episode_id": r.episode_id, "total_usd": round(float(r.total or 0), 4)}
                for r in ep_costs
            ],
        })
    finally:
        session.close()


# ─── Voice Management ─────────────────────────────────────────────────────────

ELEVENLABS_PRESET_VOICES = [
    {"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel", "category": "premade", "gender": "female", "accent": "American", "style": "Calm, Narration"},
    {"voice_id": "AZnzlk1XvdvUeBnXmlld", "name": "Domi", "category": "premade", "gender": "female", "accent": "American", "style": "Strong, Assertive"},
    {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella", "category": "premade", "gender": "female", "accent": "American", "style": "Soft, Gentle"},
    {"voice_id": "ErXwobaYiN019PkySvjV", "name": "Antoni", "category": "premade", "gender": "male", "accent": "American", "style": "Well-rounded, Calm"},
    {"voice_id": "MF3mGyEYCl7XYWbV9V6O", "name": "Elli", "category": "premade", "gender": "female", "accent": "American", "style": "Emotional, Youthful"},
    {"voice_id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh", "category": "premade", "gender": "male", "accent": "American", "style": "Deep, Narrator"},
    {"voice_id": "VR6AewLTigWG4xSOukaG", "name": "Arnold", "category": "premade", "gender": "male", "accent": "American", "style": "Crisp, Authoritative"},
    {"voice_id": "pNInz6obpgDQGcFmaJgB", "name": "Adam", "category": "premade", "gender": "male", "accent": "American", "style": "Deep, Narration"},
    {"voice_id": "yoZ06aMxZJJ28mfd3POQ", "name": "Sam", "category": "premade", "gender": "male", "accent": "American", "style": "Raspy, Dynamic"},
    {"voice_id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel", "category": "premade", "gender": "male", "accent": "British", "style": "Authoritative, Deep"},
    {"voice_id": "N2lVS1w4EtoT3dr4eOWO", "name": "Callum", "category": "premade", "gender": "male", "accent": "Transatlantic", "style": "Intense, Hoarse"},
    {"voice_id": "TX3LPaxmHKxFdv7VOQHJ", "name": "Liam", "category": "premade", "gender": "male", "accent": "American", "style": "Articulate, Confident"},
    {"voice_id": "bIHbv24MWmeRgasZH58o", "name": "Will", "category": "premade", "gender": "male", "accent": "American", "style": "Friendly, News"},
    {"voice_id": "nPczCjzI2devNBz1zQrb", "name": "Brian", "category": "premade", "gender": "male", "accent": "American", "style": "Narration, Deep"},
    {"voice_id": "SOYHLrjzK2X1ezoPC6cr", "name": "Harry", "category": "premade", "gender": "male", "accent": "American", "style": "Anxious, Energetic"},
    {"voice_id": "IKne3meq5aSn9XLyUdCD", "name": "Charlie", "category": "premade", "gender": "male", "accent": "Australian", "style": "Casual, Natural"},
    {"voice_id": "JBFqnCBsd6RMkjVDRZzb", "name": "George", "category": "premade", "gender": "male", "accent": "British", "style": "Warm, Raspy"},
    {"voice_id": "XB0fDUnXU5powFXDhCwa", "name": "Charlotte", "category": "premade", "gender": "female", "accent": "Swedish", "style": "Seductive, Calm"},
    {"voice_id": "Xb7hH8MSUJpSbSDYk0k2", "name": "Alice", "category": "premade", "gender": "female", "accent": "British", "style": "Confident, Middle-aged"},
    {"voice_id": "pqHfZKP75CvOlQylNhV4", "name": "Bill", "category": "premade", "gender": "male", "accent": "American", "style": "Trustworthy, Authoritative"},
]


@app.get("/api/voices")
async def api_list_voices():
    """List available ElevenLabs voices (premade catalog + API voices if permission exists)."""
    try:
        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not api_key:
            return JSONResponse({"error": "ELEVENLABS_API_KEY not set", "voices": []}, status_code=400)

        voices = list(ELEVENLABS_PRESET_VOICES)

        # Try to fetch user's actual voice library (works if API key has voices_read)
        import requests as req
        try:
            resp = req.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": api_key}, timeout=15)
            if resp.status_code == 200:
                known_ids = {v["voice_id"] for v in voices}
                for v in resp.json().get("voices", []):
                    if v["voice_id"] not in known_ids:
                        voices.append({
                            "voice_id": v["voice_id"], "name": v.get("name", "Unknown"),
                            "category": v.get("category", "custom"), "gender": "",
                            "accent": "", "style": "Custom",
                        })
        except Exception:
            pass

        default_vid = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        if default_vid and not any(v["voice_id"] == default_vid for v in voices):
            voices.insert(0, {"voice_id": default_vid, "name": "Current Default", "category": "configured", "gender": "", "accent": "", "style": ""})

        return JSONResponse({"voices": voices})
    except Exception as exc:
        return JSONResponse({"error": str(exc), "voices": []}, status_code=500)


@app.post("/api/scenes/{scene_id}/regenerate-voice")
async def api_regenerate_voice(scene_id: int, request: Request):
    """Regenerate voiceover for a single scene with optional voice_id override."""
    body = await request.json()
    voice_id = body.get("voice_id") or None

    session = SessionLocal()
    try:
        scene = session.execute(
            select(Scene).where(Scene.id == scene_id)
        ).scalar_one_or_none()
        if not scene:
            return JSONResponse({"error": "Scene not found"}, status_code=404)

        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        default_voice = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip()
        vid = voice_id or default_voice
        if not api_key or not vid:
            return JSONResponse({"error": "ElevenLabs API key or voice ID not configured"}, status_code=400)

        episode_number = scene.episode.episode_number if scene.episode else 0
        output_path = settings.output_root / "audio" / f"episode_{episode_number:04d}_scene_{scene.scene_order:02d}.mp3"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        import requests as req
        endpoint = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
        headers = {"xi-api-key": api_key, "Accept": "audio/mpeg", "Content-Type": "application/json"}
        payload = {"text": scene.narration_text, "model_id": model_id, "voice_settings": {"stability": 0.4, "similarity_boost": 0.75}}
        response = req.post(endpoint, headers=headers, json=payload, timeout=240)
        response.raise_for_status()
        if len(response.content) < 1024:
            return JSONResponse({"error": "ElevenLabs returned empty audio"}, status_code=500)
        output_path.write_bytes(response.content)

        scene.audio_file_path = str(output_path)
        session.add(scene)
        session.commit()

        audio_url = _file_to_media_url(str(output_path))
        return JSONResponse({
            "success": True,
            "scene_id": scene_id,
            "audio_url": audio_url,
            "voice_id": vid,
        })
    except Exception as exc:
        session.rollback()
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        session.close()


@app.post("/api/characters/{character_id}/voice")
async def api_set_character_voice(character_id: int, request: Request):
    """Assign a voice_id to a character."""
    body = await request.json()
    voice_id = body.get("voice_id") or None

    session = SessionLocal()
    try:
        char = session.execute(
            select(Character).where(Character.id == character_id)
        ).scalar_one_or_none()
        if not char:
            return JSONResponse({"error": "Character not found"}, status_code=404)

        char.voice_id = voice_id
        session.add(char)
        session.commit()
        return JSONResponse({"success": True, "character_id": character_id, "voice_id": voice_id})
    except Exception as exc:
        session.rollback()
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        session.close()


# ─── Retry Endpoints ─────────────────────────────────────────────────────────

def _get_or_create_job(session, episode_id: int):
    """Find the latest job for an episode and reuse it, or create one only if none exists."""
    job = session.execute(
        select(VideoJob)
        .where(VideoJob.episode_id == episode_id)
        .order_by(desc(VideoJob.id))
        .limit(1)
    ).scalar_one_or_none()

    if job:
        job.status = JobStatus.RUNNING
        job.completed_at = None
        session.add(job)
        session.commit()
        return job

    from database.connection import create_video_job
    job = create_video_job(session=session)
    job.episode_id = episode_id
    session.add(job)
    session.commit()
    return job


@app.post("/api/generate/new")
async def api_generate_new():
    """Trigger a brand-new episode pipeline in background thread."""
    def _run():
        try:
            from main import run_pipeline
            run_pipeline()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("Background pipeline failed: %s", exc)
            try:
                from database.connection import SessionLocal as SL, log_job_step
                from database.models import StepStatus, JobStatus
                from sqlalchemy import desc
                s = SL()
                job = s.execute(select(VideoJob).order_by(desc(VideoJob.id)).limit(1)).scalar_one_or_none()
                if job and job.status == JobStatus.RUNNING:
                    job.status = JobStatus.FAILED
                    s.add(job)
                    log_job_step(s, job.id, "pipeline", StepStatus.FAILED, f"Pipeline crashed: {str(exc)[:500]}")
                    s.commit()
                s.close()
            except Exception:
                pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return JSONResponse({"status": "started", "message": "New episode pipeline started."})


def _acquire_episode_lock(episode_id: int, action: str) -> str | None:
    """Try to lock an episode for a pipeline action. Returns error message if already locked."""
    with _episode_locks_mu:
        existing = _episode_locks.get(episode_id)
        if existing:
            return f"Episode {episode_id} already has '{existing}' running. Wait for it to finish."
        _episode_locks[episode_id] = action
    return None


def _release_episode_lock(episode_id: int) -> None:
    with _episode_locks_mu:
        _episode_locks.pop(episode_id, None)


@app.post("/api/episodes/{episode_id}/retry-images")
async def api_retry_images(episode_id: int):
    """Re-run DALL-E image generation for scenes missing images, reusing the existing job."""
    lock_err = _acquire_episode_lock(episode_id, "images")
    if lock_err:
        return JSONResponse({"error": lock_err}, status_code=409)

    session = SessionLocal()
    try:
        episode = session.execute(
            select(Episode).where(Episode.id == episode_id)
        ).scalar_one_or_none()
        if not episode:
            _release_episode_lock(episode_id)
            return JSONResponse({"error": "Episode not found"}, status_code=404)

        job = _get_or_create_job(session, episode_id)
        job_id = job.id

        def _run(ep_id: int, jid: int):
            try:
                from pipeline.image_generator import generate_scene_image, IMAGES_DIR
                from pipeline.cost_tracker import log_dalle
                from database.connection import SessionLocal as SL, log_job_step
                from database.models import StepStatus
                from sqlalchemy import select as sel
                from pathlib import Path
                import logging
                log = logging.getLogger(__name__)

                s = SL()
                try:
                    ep = s.execute(sel(Episode).where(Episode.id == ep_id)).scalar_one()
                    scenes = list(s.execute(sel(Scene).where(Scene.episode_id == ep_id).order_by(Scene.scene_order)).scalars())

                    log_job_step(s, jid, "image_gen", StepStatus.STARTED,
                                 f"Regenerating DALL-E images for {len(scenes)} scenes.")
                    s.commit()

                    for scene in scenes:
                        img_path = generate_scene_image(s, ep, scene)
                        log_dalle(s, ep.id, jid, f"scene_{scene.scene_order}_image",
                                  size="1792x1024", quality="hd")
                        s.commit()

                    log_job_step(s, jid, "image_gen", StepStatus.SUCCESS,
                                 f"Generated {len(scenes)} DALL-E images.")
                    s.commit()
                    log.info("DALL-E image retry completed for Episode #%s (Job #%s)", ep.episode_number, jid)
                except Exception as exc:
                    s.rollback()
                    log.exception("Retry images failed for ep %s", ep_id)
                    try:
                        log_job_step(s, jid, "image_gen", StepStatus.FAILED, str(exc)[:500])
                        s.commit()
                    except Exception:
                        pass
                finally:
                    s.close()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Retry images outer error.")
            finally:
                _release_episode_lock(ep_id)

        thread = threading.Thread(target=_run, args=(episode_id, job_id), daemon=True)
        thread.start()
        return JSONResponse({"status": "started", "message": f"DALL-E image retry started on Job #{job_id}.", "job_id": job_id})
    finally:
        session.close()


@app.post("/api/episodes/{episode_id}/retry-kenburns")
async def api_retry_kenburns(episode_id: int):
    """Re-run Ken Burns animation from existing images, reusing the existing job."""
    lock_err = _acquire_episode_lock(episode_id, "kenburns")
    if lock_err:
        return JSONResponse({"error": lock_err}, status_code=409)

    session = SessionLocal()
    try:
        episode = session.execute(
            select(Episode).where(Episode.id == episode_id)
        ).scalar_one_or_none()
        if not episode:
            _release_episode_lock(episode_id)
            return JSONResponse({"error": "Episode not found"}, status_code=404)

        job = _get_or_create_job(session, episode_id)
        job_id = job.id

        def _run(ep_id: int, jid: int):
            try:
                from pipeline.video_generator import apply_ken_burns, merge_episode_assets
                from pipeline.image_generator import IMAGES_DIR
                from pipeline.music import mix_episode_music
                from pipeline.thumbnail import ThumbnailGenerator
                from pipeline.shorts import ShortsGenerator
                from database.connection import SessionLocal as SL, log_job_step, mark_job_ready, set_episode_status
                from database.models import EpisodeStatus, StepStatus, SceneStatus
                from sqlalchemy import select as sel
                from pathlib import Path
                import logging
                log = logging.getLogger(__name__)

                s = SL()
                try:
                    ep = s.execute(sel(Episode).where(Episode.id == ep_id)).scalar_one()
                    scenes = list(s.execute(sel(Scene).where(Scene.episode_id == ep_id).order_by(Scene.scene_order)).scalars())

                    log_job_step(s, jid, "ken_burns", StepStatus.STARTED,
                                 f"Applying Ken Burns effects to {len(scenes)} scenes.")
                    s.commit()

                    for scene in scenes:
                        img_path = IMAGES_DIR / f"episode_{ep.episode_number:04d}_scene_{scene.scene_order:02d}.png"
                        if not img_path.exists():
                            raise FileNotFoundError(f"Image not found: {img_path}. Run DALL-E image generation first.")

                        clip_path = settings.clips_dir / f"episode_{ep.episode_number:04d}_scene_{scene.scene_order:02d}.mp4"
                        scene_type = scene.scene_type if scene.scene_type else __import__('database.models', fromlist=['SceneType']).SceneType.OTHER
                        apply_ken_burns(
                            image_path=img_path,
                            output_path=clip_path,
                            duration_seconds=float(settings.default_scene_duration_seconds),
                            scene_type=scene_type,
                        )
                        scene.video_clip_path = str(clip_path)
                        scene.status = SceneStatus.VIDEO_DONE
                        s.add(scene)
                        s.commit()

                    log_job_step(s, jid, "ken_burns", StepStatus.SUCCESS,
                                 f"Ken Burns effects applied to {len(scenes)} clips.")
                    s.commit()

                    # Auto-continue: Merge
                    log_job_step(s, jid, "editing", StepStatus.STARTED, "Merging clips and audio with FFmpeg.")
                    s.commit()

                    mixed_audio = mix_episode_music(ep.episode_number, scenes)
                    clip_paths = [Path(sc.video_clip_path) for sc in scenes if sc.video_clip_path]
                    audio_paths = mixed_audio if mixed_audio else [Path(sc.audio_file_path) for sc in scenes if sc.audio_file_path]
                    subtitle_paths = [Path(sc.subtitle_file_path) for sc in scenes if sc.subtitle_file_path and Path(sc.subtitle_file_path).exists()]
                    final_path = settings.final_dir / f"episode_{ep.episode_number:04d}.mp4"

                    duration = merge_episode_assets(
                        episode_number=ep.episode_number,
                        clip_paths=clip_paths,
                        audio_paths=audio_paths,
                        output_path=final_path,
                        subtitle_paths=subtitle_paths,
                    )
                    job_obj = s.execute(sel(VideoJob).where(VideoJob.id == jid)).scalar_one()
                    mark_job_ready(s, job=job_obj, final_video_path=str(final_path), duration_seconds=duration)
                    set_episode_status(s, ep, EpisodeStatus.READY)
                    log_job_step(s, jid, "editing", StepStatus.SUCCESS, f"Final video: {final_path}")
                    s.commit()

                    try:
                        thumb_gen = ThumbnailGenerator()
                        thumb_gen.generate_thumbnail(session=s, job_id=jid, episode=ep, job=job_obj)
                        s.commit()
                    except Exception as exc:
                        log.warning("Thumbnail generation failed (non-fatal): %s", exc)
                        log_job_step(s, jid, "thumbnail", StepStatus.FAILED, str(exc)[:300])
                        s.commit()

                    try:
                        shorts_gen = ShortsGenerator()
                        shorts_gen.generate_short(session=s, job_id=jid, episode=ep)
                        s.commit()
                    except Exception as exc:
                        log.warning("Shorts generation failed (non-fatal): %s", exc)
                        log_job_step(s, jid, "shorts", StepStatus.FAILED, str(exc)[:300])
                        s.commit()

                    log.info("Ken Burns retry pipeline completed for Episode #%s (Job #%s)", ep.episode_number, jid)
                except Exception as exc:
                    s.rollback()
                    log.exception("Retry Ken Burns failed for ep %s", ep_id)
                    try:
                        log_job_step(s, jid, "ken_burns", StepStatus.FAILED, str(exc)[:500])
                        job_obj = s.execute(sel(VideoJob).where(VideoJob.id == jid)).scalar_one_or_none()
                        if job_obj:
                            job_obj.status = JobStatus.FAILED
                            s.add(job_obj)
                        s.commit()
                    except Exception:
                        pass
                finally:
                    s.close()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Retry Ken Burns outer error.")
            finally:
                _release_episode_lock(ep_id)

        thread = threading.Thread(target=_run, args=(episode_id, job_id), daemon=True)
        thread.start()
        return JSONResponse({"status": "started", "message": f"Ken Burns retry started on Job #{job_id}.", "job_id": job_id})
    finally:
        session.close()


@app.post("/api/episodes/{episode_id}/retry-video")
async def api_retry_video(episode_id: int):
    """Re-run video generation for missing scenes, reusing the existing job."""
    lock_err = _acquire_episode_lock(episode_id, "video")
    if lock_err:
        return JSONResponse({"error": lock_err}, status_code=409)

    session = SessionLocal()
    try:
        episode = session.execute(
            select(Episode).where(Episode.id == episode_id)
        ).scalar_one_or_none()
        if not episode:
            _release_episode_lock(episode_id)
            return JSONResponse({"error": "Episode not found"}, status_code=404)

        job = _get_or_create_job(session, episode_id)
        job_id = job.id

        episode.status = EpisodeStatus.EDITING
        session.add(episode)
        session.commit()

        def _run_video(ep_id: int, jid: int):
            try:
                from pipeline.video_generator import VideoGenerator, merge_episode_assets
                from pipeline.music import mix_episode_music
                from pipeline.thumbnail import ThumbnailGenerator
                from pipeline.shorts import ShortsGenerator
                from database.connection import SessionLocal as SL, log_job_step, mark_job_ready, set_episode_status
                from database.models import EpisodeStatus, StepStatus, Scene as SceneModel
                from sqlalchemy import select as sel
                from pathlib import Path
                import logging
                log = logging.getLogger(__name__)

                s = SL()
                try:
                    ep = s.execute(sel(Episode).where(Episode.id == ep_id)).scalar_one()
                    scenes = list(s.execute(
                        sel(SceneModel).where(SceneModel.episode_id == ep_id).order_by(SceneModel.scene_order)
                    ).scalars())
                    skip = [sc for sc in scenes if sc.video_clip_path and Path(sc.video_clip_path).exists()]
                    need = [sc for sc in scenes if not sc.video_clip_path or not Path(sc.video_clip_path).exists()]

                    if need:
                        gen = VideoGenerator()
                        gen.generate_episode_clips(session=s, job_id=jid, episode=ep, scenes_override=need)
                        s.commit()

                    scenes = list(s.execute(
                        sel(SceneModel).where(SceneModel.episode_id == ep_id).order_by(SceneModel.scene_order)
                    ).scalars())

                    log_job_step(s, jid, "editing", StepStatus.STARTED, "Merging clips and audio with FFmpeg.")
                    s.commit()

                    mixed_audio = mix_episode_music(ep.episode_number, scenes)
                    clip_paths = [Path(sc.video_clip_path) for sc in scenes if sc.video_clip_path]
                    audio_paths = mixed_audio if mixed_audio else [Path(sc.audio_file_path) for sc in scenes if sc.audio_file_path]
                    subtitle_paths = [Path(sc.subtitle_file_path) for sc in scenes if sc.subtitle_file_path and Path(sc.subtitle_file_path).exists()]
                    final_path = settings.final_dir / f"episode_{ep.episode_number:04d}.mp4"

                    duration = merge_episode_assets(
                        episode_number=ep.episode_number,
                        clip_paths=clip_paths,
                        audio_paths=audio_paths,
                        output_path=final_path,
                        subtitle_paths=subtitle_paths,
                    )
                    job_obj = s.execute(sel(VideoJob).where(VideoJob.id == jid)).scalar_one()
                    mark_job_ready(s, job=job_obj, final_video_path=str(final_path), duration_seconds=duration)
                    set_episode_status(s, ep, EpisodeStatus.READY)
                    log_job_step(s, jid, "editing", StepStatus.SUCCESS, f"Final video: {final_path}")
                    s.commit()

                    try:
                        thumb_gen = ThumbnailGenerator()
                        thumb_gen.generate_thumbnail(session=s, job_id=jid, episode=ep, job=job_obj)
                        s.commit()
                    except Exception as exc:
                        log.warning("Thumbnail generation failed (non-fatal): %s", exc)
                        log_job_step(s, jid, "thumbnail", StepStatus.FAILED, str(exc)[:300])
                        s.commit()

                    try:
                        shorts_gen = ShortsGenerator()
                        shorts_gen.generate_short(session=s, job_id=jid, episode=ep)
                        s.commit()
                    except Exception as exc:
                        log.warning("Shorts generation failed (non-fatal): %s", exc)
                        log_job_step(s, jid, "shorts", StepStatus.FAILED, str(exc)[:300])
                        s.commit()

                    log.info("Pipeline completed for episode %s (Job #%s)", ep.episode_number, jid)
                except Exception as exc:
                    s.rollback()
                    log.exception("Retry video pipeline failed for ep %s", ep_id)
                    try:
                        log_job_step(s, jid, "pipeline", StepStatus.FAILED, str(exc)[:500])
                        job_obj = s.execute(sel(VideoJob).where(VideoJob.id == jid)).scalar_one_or_none()
                        if job_obj:
                            job_obj.status = JobStatus.FAILED
                            s.add(job_obj)
                        s.commit()
                    except Exception:
                        pass
                finally:
                    s.close()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Retry video gen outer error.")
            finally:
                _release_episode_lock(ep_id)

        thread = threading.Thread(target=_run_video, args=(episode_id, job_id), daemon=True)
        thread.start()
        return JSONResponse({"status": "started", "message": f"Video retry started on Job #{job_id}.", "job_id": job_id})
    finally:
        session.close()


@app.post("/api/episodes/{episode_id}/retry-merge")
async def api_retry_merge(episode_id: int):
    """Re-run FFmpeg merge, reusing the existing job."""
    lock_err = _acquire_episode_lock(episode_id, "merge")
    if lock_err:
        return JSONResponse({"error": lock_err}, status_code=409)

    session = SessionLocal()
    try:
        episode = session.execute(
            select(Episode).where(Episode.id == episode_id)
        ).scalar_one_or_none()
        if not episode:
            _release_episode_lock(episode_id)
            return JSONResponse({"error": "Episode not found"}, status_code=404)

        job = _get_or_create_job(session, episode_id)
        job_id = job.id

        def _run_merge(ep_id: int, jid: int):
            try:
                from pipeline.video_generator import merge_episode_assets
                from pipeline.music import mix_episode_music
                from pipeline.thumbnail import ThumbnailGenerator
                from pipeline.shorts import ShortsGenerator
                from database.connection import SessionLocal as SL, log_job_step, mark_job_ready, set_episode_status
                from database.models import EpisodeStatus, StepStatus
                from sqlalchemy import select as sel
                from pathlib import Path
                import logging
                log = logging.getLogger(__name__)

                s = SL()
                try:
                    ep = s.execute(sel(Episode).where(Episode.id == ep_id)).scalar_one()
                    scenes = list(s.execute(sel(Scene).where(Scene.episode_id == ep_id).order_by(Scene.scene_order)).scalars())

                    log_job_step(s, jid, "editing", StepStatus.STARTED, "Re-merging clips and audio.")
                    s.commit()

                    mixed_audio = mix_episode_music(ep.episode_number, scenes)
                    clip_paths = [Path(sc.video_clip_path) for sc in scenes if sc.video_clip_path]
                    audio_paths = mixed_audio if mixed_audio else [Path(sc.audio_file_path) for sc in scenes if sc.audio_file_path]
                    subtitle_paths = [Path(sc.subtitle_file_path) for sc in scenes if sc.subtitle_file_path and Path(sc.subtitle_file_path).exists()]
                    final_path = settings.final_dir / f"episode_{ep.episode_number:04d}.mp4"

                    duration = merge_episode_assets(
                        episode_number=ep.episode_number,
                        clip_paths=clip_paths,
                        audio_paths=audio_paths,
                        output_path=final_path,
                        subtitle_paths=subtitle_paths,
                    )

                    job_obj = s.execute(sel(VideoJob).where(VideoJob.id == jid)).scalar_one()
                    mark_job_ready(s, job=job_obj, final_video_path=str(final_path), duration_seconds=duration)
                    set_episode_status(s, ep, EpisodeStatus.READY)
                    log_job_step(s, jid, "editing", StepStatus.SUCCESS, f"Re-merged: {final_path}")
                    s.commit()

                    # Auto-continue: Thumbnail
                    try:
                        thumb_gen = ThumbnailGenerator()
                        thumb_gen.generate_thumbnail(session=s, job_id=jid, episode=ep, job=job_obj)
                        s.commit()
                    except Exception as exc:
                        log.warning("Thumbnail generation failed (non-fatal): %s", exc)
                        log_job_step(s, jid, "thumbnail", StepStatus.FAILED, str(exc)[:300])
                        s.commit()

                    # Auto-continue: Shorts
                    try:
                        shorts_gen = ShortsGenerator()
                        shorts_gen.generate_short(session=s, job_id=jid, episode=ep)
                        s.commit()
                    except Exception as exc:
                        log.warning("Shorts generation failed (non-fatal): %s", exc)
                        log_job_step(s, jid, "shorts", StepStatus.FAILED, str(exc)[:300])
                        s.commit()

                    log.info("Merge pipeline completed for Episode #%s (Job #%s)", ep.episode_number, jid)
                except Exception as exc:
                    s.rollback()
                    log.exception("Retry merge failed for ep %s", ep_id)
                    try:
                        log_job_step(s, jid, "editing", StepStatus.FAILED, str(exc))
                        job_obj = s.execute(sel(VideoJob).where(VideoJob.id == jid)).scalar_one_or_none()
                        if job_obj:
                            job_obj.status = JobStatus.FAILED
                            s.add(job_obj)
                        s.commit()
                    except Exception:
                        pass
                finally:
                    s.close()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Retry merge outer error.")
            finally:
                _release_episode_lock(ep_id)

        thread = threading.Thread(target=_run_merge, args=(episode_id, job_id), daemon=True)
        thread.start()
        return JSONResponse({"status": "started", "message": f"Merge retry started on Job #{job_id}.", "job_id": job_id})
    finally:
        session.close()


@app.post("/api/episodes/{episode_id}/retry-full")
async def api_retry_full(episode_id: int):
    """Re-run full pipeline from video gen onwards, reusing the existing job."""
    lock_err = _acquire_episode_lock(episode_id, "full")
    if lock_err:
        return JSONResponse({"error": lock_err}, status_code=409)

    session = SessionLocal()
    try:
        episode = session.execute(
            select(Episode).where(Episode.id == episode_id)
        ).scalar_one_or_none()
        if not episode:
            _release_episode_lock(episode_id)
            return JSONResponse({"error": "Episode not found"}, status_code=404)

        job = _get_or_create_job(session, episode_id)
        job_id = job.id

        def _run_full(ep_id: int, jid: int):
            try:
                from pipeline.video_generator import VideoGenerator, merge_episode_assets
                from pipeline.music import mix_episode_music
                from pipeline.thumbnail import ThumbnailGenerator
                from pipeline.shorts import ShortsGenerator
                from database.connection import SessionLocal as SL, log_job_step, mark_job_ready, set_episode_status
                from database.models import EpisodeStatus, StepStatus
                from sqlalchemy import select as sel
                from pathlib import Path
                import logging
                log = logging.getLogger(__name__)

                s = SL()
                try:
                    ep = s.execute(sel(Episode).where(Episode.id == ep_id)).scalar_one()
                    scenes = list(s.execute(sel(Scene).where(Scene.episode_id == ep_id).order_by(Scene.scene_order)).scalars())

                    gen = VideoGenerator()
                    gen.generate_episode_clips(session=s, job_id=jid, episode=ep)
                    s.commit()

                    mixed_audio = mix_episode_music(ep.episode_number, scenes)

                    log_job_step(s, jid, "editing", StepStatus.STARTED, "Merging clips and audio with FFmpeg.")
                    s.commit()
                    clip_paths = [Path(sc.video_clip_path) for sc in scenes if sc.video_clip_path]
                    audio_paths = mixed_audio if mixed_audio else [Path(sc.audio_file_path) for sc in scenes if sc.audio_file_path]
                    subtitle_paths = [Path(sc.subtitle_file_path) for sc in scenes if sc.subtitle_file_path and Path(sc.subtitle_file_path).exists()]
                    final_path = settings.final_dir / f"episode_{ep.episode_number:04d}.mp4"

                    duration = merge_episode_assets(
                        episode_number=ep.episode_number,
                        clip_paths=clip_paths,
                        audio_paths=audio_paths,
                        output_path=final_path,
                        subtitle_paths=subtitle_paths,
                    )
                    job_obj = s.execute(sel(VideoJob).where(VideoJob.id == jid)).scalar_one()
                    mark_job_ready(s, job=job_obj, final_video_path=str(final_path), duration_seconds=duration)
                    set_episode_status(s, ep, EpisodeStatus.READY)
                    log_job_step(s, jid, "editing", StepStatus.SUCCESS, f"Final video: {final_path}")
                    s.commit()

                    thumb_gen = ThumbnailGenerator()
                    thumb_gen.generate_thumbnail(session=s, job_id=jid, episode=ep, job=job_obj)
                    s.commit()

                    shorts_gen = ShortsGenerator()
                    shorts_gen.generate_short(session=s, job_id=jid, episode=ep)
                    s.commit()

                    log.info("Retry-full pipeline completed for Episode #%s (Job #%s)", ep.episode_number, jid)
                except Exception as exc:
                    s.rollback()
                    log.exception("Retry-full failed for ep %s", ep_id)
                    try:
                        job_obj = s.execute(sel(VideoJob).where(VideoJob.id == jid)).scalar_one_or_none()
                        if job_obj:
                            job_obj.status = JobStatus.FAILED
                            s.add(job_obj)
                            log_job_step(s, jid, "pipeline", StepStatus.FAILED, str(exc))
                            s.commit()
                    except Exception:
                        pass
                finally:
                    s.close()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Retry-full outer error.")
            finally:
                _release_episode_lock(ep_id)

        thread = threading.Thread(target=_run_full, args=(episode_id, job_id), daemon=True)
        thread.start()
        return JSONResponse({"status": "started", "message": f"Full retry started on Job #{job_id}.", "job_id": job_id})
    finally:
        session.close()


@app.post("/api/episodes/{episode_id}/retry-thumbnail")
async def api_retry_thumbnail(episode_id: int):
    """Re-generate thumbnail, reusing the existing job."""
    session = SessionLocal()
    try:
        episode = session.execute(
            select(Episode).where(Episode.id == episode_id)
        ).scalar_one_or_none()
        if not episode:
            return JSONResponse({"error": "Episode not found"}, status_code=404)

        job = _get_or_create_job(session, episode_id)
        job_id = job.id

        def _run(ep_id: int, jid: int):
            try:
                from pipeline.thumbnail import ThumbnailGenerator
                from database.connection import SessionLocal as SL, log_job_step
                from database.models import StepStatus
                from sqlalchemy import select as sel
                import logging

                s = SL()
                try:
                    ep = s.execute(sel(Episode).where(Episode.id == ep_id)).scalar_one()
                    job_obj = s.execute(sel(VideoJob).where(VideoJob.id == jid)).scalar_one()

                    gen = ThumbnailGenerator()
                    gen.generate_thumbnail(session=s, job_id=jid, episode=ep, job=job_obj)
                    s.commit()
                except Exception as exc:
                    s.rollback()
                    logging.getLogger(__name__).exception("Retry thumbnail failed for ep %s", ep_id)
                    try:
                        log_job_step(s, jid, "thumbnail", StepStatus.FAILED, str(exc))
                        s.commit()
                    except Exception:
                        pass
                finally:
                    s.close()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Retry thumbnail outer error.")

        thread = threading.Thread(target=_run, args=(episode_id, job_id), daemon=True)
        thread.start()
        return JSONResponse({"status": "started", "message": f"Thumbnail retry on Job #{job_id}.", "job_id": job_id})
    finally:
        session.close()


@app.post("/api/episodes/{episode_id}/retry-shorts")
async def api_retry_shorts(episode_id: int):
    """Re-generate YouTube Short, reusing the existing job."""
    session = SessionLocal()
    try:
        episode = session.execute(
            select(Episode).where(Episode.id == episode_id)
        ).scalar_one_or_none()
        if not episode:
            return JSONResponse({"error": "Episode not found"}, status_code=404)

        job = _get_or_create_job(session, episode_id)
        job_id = job.id

        def _run(ep_id: int, jid: int):
            try:
                from pipeline.shorts import ShortsGenerator
                from database.connection import SessionLocal as SL, log_job_step
                from database.models import StepStatus
                from sqlalchemy import select as sel
                import logging

                s = SL()
                try:
                    ep = s.execute(sel(Episode).where(Episode.id == ep_id)).scalar_one()

                    gen = ShortsGenerator()
                    gen.generate_short(session=s, job_id=jid, episode=ep)
                    s.commit()
                except Exception as exc:
                    s.rollback()
                    logging.getLogger(__name__).exception("Retry shorts failed for ep %s", ep_id)
                    try:
                        log_job_step(s, jid, "shorts", StepStatus.FAILED, str(exc))
                        s.commit()
                    except Exception:
                        pass
                finally:
                    s.close()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Retry shorts outer error.")

        thread = threading.Thread(target=_run, args=(episode_id, job_id), daemon=True)
        thread.start()
        return JSONResponse({"status": "started", "message": f"Shorts retry on Job #{job_id}.", "job_id": job_id})
    finally:
        session.close()


@app.post("/api/episodes/{episode_id}/retry-upload")
async def api_retry_upload(episode_id: int):
    """Re-upload episode to YouTube, reusing the existing job."""
    import os as _os
    if not _os.getenv("YOUTUBE_CLIENT_SECRET_PATH", ""):
        return JSONResponse({"error": "YouTube not configured — YOUTUBE_CLIENT_SECRET_PATH missing."}, status_code=400)

    session = SessionLocal()
    try:
        episode = session.execute(
            select(Episode).where(Episode.id == episode_id)
        ).scalar_one_or_none()
        if not episode:
            return JSONResponse({"error": "Episode not found"}, status_code=404)

        job = _get_or_create_job(session, episode_id)
        job_id = job.id

        def _run(ep_id: int, jid: int):
            try:
                from pipeline.youtube_upload import YouTubeUploader
                from database.connection import SessionLocal as SL, log_job_step
                from database.models import StepStatus, EpisodeSEO, Short
                from sqlalchemy import select as sel
                from pathlib import Path
                import logging

                s = SL()
                try:
                    ep = s.execute(sel(Episode).where(Episode.id == ep_id)).scalar_one()
                    seo = s.execute(sel(EpisodeSEO).where(EpisodeSEO.episode_id == ep_id)).scalar_one_or_none()
                    job_obj = s.execute(sel(VideoJob).where(VideoJob.id == jid)).scalar_one()

                    video_path = Path(job_obj.final_video_path) if job_obj.final_video_path else None
                    thumb_path = Path(job_obj.thumbnail_path) if job_obj.thumbnail_path else None

                    if not video_path or not video_path.exists():
                        all_jobs = list(s.execute(
                            sel(VideoJob).where(VideoJob.episode_id == ep_id, VideoJob.final_video_path.isnot(None))
                        ).scalars())
                        for j in all_jobs:
                            if j.final_video_path and Path(j.final_video_path).exists():
                                video_path = Path(j.final_video_path)
                            if j.thumbnail_path and Path(j.thumbnail_path).exists():
                                thumb_path = Path(j.thumbnail_path)

                    if not video_path or not video_path.exists():
                        log_job_step(s, jid, "upload", StepStatus.FAILED, "No final video found to upload.")
                        s.commit()
                        return

                    uploader = YouTubeUploader()
                    uploader.upload_video(session=s, job_id=jid, episode=ep, video_path=video_path, thumbnail_path=thumb_path, seo=seo)

                    short = s.execute(sel(Short).where(Short.episode_id == ep_id)).scalar_one_or_none()
                    if short and short.file_path and Path(short.file_path).exists():
                        uploader.upload_short(session=s, job_id=jid, episode=ep, short=short, seo=seo)

                    s.commit()
                except Exception as exc:
                    s.rollback()
                    logging.getLogger(__name__).exception("Retry upload failed for ep %s", ep_id)
                    try:
                        log_job_step(s, jid, "upload", StepStatus.FAILED, str(exc))
                        s.commit()
                    except Exception:
                        pass
                finally:
                    s.close()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Retry upload outer error.")

        thread = threading.Thread(target=_run, args=(episode_id, job_id), daemon=True)
        thread.start()
        return JSONResponse({"status": "started", "message": f"Upload retry on Job #{job_id}.", "job_id": job_id})
    finally:
        session.close()


# ─── Custom Video Creator ─────────────────────────────────────────────────────

@app.get("/create", response_class=HTMLResponse)
async def create_page(request: Request):
    return templates.TemplateResponse("create.html", {"request": request})


@app.post("/api/custom/preview-voice")
async def api_custom_preview_voice(request: Request):
    """Generate a short voiceover preview for custom text."""
    body = await request.json()
    text = (body.get("text") or "").strip()
    voice_id = (body.get("voice_id") or "").strip()
    if not text:
        return JSONResponse({"error": "Text is required"}, status_code=400)

    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    vid = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    if not api_key or not vid:
        return JSONResponse({"error": "ElevenLabs not configured"}, status_code=400)

    import requests as req
    model_id = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip()
    resp = req.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
        headers={"xi-api-key": api_key, "Accept": "audio/mpeg", "Content-Type": "application/json"},
        json={"text": text[:500], "model_id": model_id, "voice_settings": {"stability": 0.4, "similarity_boost": 0.75}},
        timeout=60,
    )
    resp.raise_for_status()
    preview_dir = settings.output_root / "custom" / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    import hashlib
    fname = hashlib.md5(f"{vid}_{text[:100]}".encode()).hexdigest()[:12] + ".mp3"
    out = preview_dir / fname
    out.write_bytes(resp.content)
    return JSONResponse({"audio_url": _file_to_media_url(str(out)), "size_kb": len(resp.content) // 1024})


@app.post("/api/custom/generate")
async def api_custom_generate(request: Request):
    """Generate a full custom video from user-provided scenes."""
    body = await request.json()
    title = (body.get("title") or "Custom Video").strip()
    scenes_data = body.get("scenes") or []
    provider = (body.get("provider") or "minimax").strip()

    if not scenes_data:
        return JSONResponse({"error": "At least one scene is required."}, status_code=400)

    for i, sc in enumerate(scenes_data):
        if not sc.get("narration", "").strip():
            return JSONResponse({"error": f"Scene {i+1} narration is empty."}, status_code=400)
        if not sc.get("video_prompt", "").strip():
            return JSONResponse({"error": f"Scene {i+1} video prompt is empty."}, status_code=400)

    def _run(title: str, scenes_data: list, provider: str):
        import logging, time, requests as req
        from pathlib import Path
        from database.connection import SessionLocal as SL
        from pipeline.cost_tracker import log_elevenlabs, log_video_provider
        log = logging.getLogger("custom_video")

        s = SL()
        try:
            from database.models import VideoJob, JobStatus, StepStatus
            from database.connection import log_job_step

            job = VideoJob(status=JobStatus.RUNNING)
            s.add(job)
            s.commit()
            job_id = job.id

            custom_dir = settings.output_root / "custom" / f"job_{job_id}"
            custom_dir.mkdir(parents=True, exist_ok=True)

            api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
            model_id = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip()
            default_voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()

            audio_paths: list[Path] = []
            clip_paths: list[Path] = []

            # --- Voiceover ---
            log_job_step(s, job_id, "voiceover", StepStatus.STARTED, f"Generating voiceovers for {len(scenes_data)} scenes.")
            s.commit()

            for i, sc in enumerate(scenes_data):
                narration = sc["narration"].strip()
                voice_id = sc.get("voice_id", "").strip() or default_voice
                audio_path = custom_dir / f"scene_{i+1:02d}.mp3"

                try:
                    resp = req.post(
                        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                        headers={"xi-api-key": api_key, "Accept": "audio/mpeg", "Content-Type": "application/json"},
                        json={"text": narration, "model_id": model_id, "voice_settings": {"stability": 0.4, "similarity_boost": 0.75}},
                        timeout=240,
                    )
                    resp.raise_for_status()
                    audio_path.write_bytes(resp.content)
                    audio_paths.append(audio_path)
                    log.info("Scene %d voiceover: %d KB", i+1, len(resp.content)//1024)
                except Exception as exc:
                    log_job_step(s, job_id, "voiceover", StepStatus.FAILED, f"Scene {i+1} voiceover failed: {exc}")
                    s.commit()
                    raise

            log_job_step(s, job_id, "voiceover", StepStatus.SUCCESS, f"Generated {len(audio_paths)} voiceovers.")
            s.commit()

            # --- Video clips ---
            log_job_step(s, job_id, "video_gen", StepStatus.STARTED, f"Generating {len(scenes_data)} clips with {provider}.")
            s.commit()

            if provider in ("minimax", "wan21", "runway", "kling"):
                from pipeline.video_generator import VideoGenerator
                gen = VideoGenerator()
                for i, sc in enumerate(scenes_data):
                    prompt = sc["video_prompt"].strip()
                    clip_path = custom_dir / f"clip_{i+1:02d}.mp4"
                    try:
                        prov = gen.providers.get(provider)
                        if not prov or not prov.is_enabled():
                            raise RuntimeError(f"Provider {provider} not available")
                        prov.generate_clip(prompt=prompt, output_path=clip_path, duration_seconds=6)
                        clip_paths.append(clip_path)
                        log_job_step(s, job_id, "video_gen_scene", StepStatus.SUCCESS, f"Scene {i+1} clip generated.")
                        s.commit()
                        log.info("Scene %d clip: %.1f MB", i+1, clip_path.stat().st_size/(1024*1024))
                    except Exception as exc:
                        log_job_step(s, job_id, "video_gen", StepStatus.FAILED, f"Scene {i+1} clip failed: {exc}")
                        s.commit()
                        raise

            log_job_step(s, job_id, "video_gen", StepStatus.SUCCESS, f"Generated {len(clip_paths)} clips.")
            s.commit()

            # --- Merge ---
            log_job_step(s, job_id, "editing", StepStatus.STARTED, "Merging clips and audio.")
            s.commit()

            from pipeline.video_generator import merge_episode_assets
            final_path = custom_dir / f"{title.replace(' ', '_')[:50]}.mp4"
            duration = merge_episode_assets(
                episode_number=0,  # Custom videos don't have a formal episode number
                clip_paths=clip_paths,
                audio_paths=audio_paths,
                output_path=final_path,
            )

            job.final_video_path = str(final_path)
            job.duration_seconds = duration
            job.status = JobStatus.READY
            s.add(job)
            log_job_step(s, job_id, "editing", StepStatus.SUCCESS, f"Final video: {final_path.name} ({duration:.1f}s)")
            s.commit()

            log.info("Custom video complete: %s (%.1fs)", final_path, duration)

        except Exception as exc:
            log.exception("Custom video generation failed")
            try:
                job_obj = s.execute(
                    __import__('sqlalchemy').select(VideoJob).where(VideoJob.id == job_id)
                ).scalar_one_or_none()
                if job_obj:
                    job_obj.status = JobStatus.FAILED
                    s.add(job_obj)
                    log_job_step(s, job_id, "pipeline", StepStatus.FAILED, str(exc)[:500])
                    s.commit()
            except Exception:
                pass
        finally:
            s.close()

    thread = threading.Thread(target=_run, args=(title, scenes_data, provider), daemon=True)
    thread.start()
    return JSONResponse({"status": "started", "message": f"Custom video '{title}' generation started."})


# ─── Settings ────────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings,
    })


@app.post("/api/settings/provider-order")
async def api_update_provider_order(request: Request):
    """Update VIDEO_PROVIDER_ORDER in .env and reload settings."""
    global settings  # noqa: PLW0603
    try:
        body = await request.json()
        order = body.get("order", [])
        if not order or not isinstance(order, list):
            return JSONResponse({"error": "order must be a non-empty list"}, status_code=400)

        from config.settings import VALID_PROVIDERS
        cleaned = [p.strip().lower() for p in order if p.strip().lower() in VALID_PROVIDERS]
        if not cleaned:
            return JSONResponse({"error": f"No valid providers. Valid: {VALID_PROVIDERS}"}, status_code=400)

        order_str = ",".join(cleaned)
        from config.settings import update_env_value, reload_settings
        update_env_value("VIDEO_PROVIDER_ORDER", order_str)
        settings = reload_settings(require_api_keys=False)

        return JSONResponse({
            "status": "ok",
            "message": f"Provider order updated to: {order_str}",
            "provider_order": list(settings.video_provider_order),
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/settings/image-provider")
async def api_set_image_provider(request: Request):
    """Switch image provider between dall-e and midjourney."""
    global settings  # noqa: PLW0603
    try:
        body = await request.json()
        provider = (body.get("provider") or "").strip().lower()
        if provider not in ("dall-e", "midjourney"):
            return JSONResponse({"error": "provider must be 'dall-e' or 'midjourney'"}, status_code=400)

        from config.settings import update_env_value, reload_settings
        update_env_value("IMAGE_PROVIDER", provider)
        if provider == "midjourney":
            update_env_value("MIDJOURNEY_ENABLED", "true")
        settings = reload_settings(require_api_keys=False)

        return JSONResponse({
            "status": "ok",
            "image_provider": provider,
            "message": f"Image provider switched to {provider}.",
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/settings/reload")
async def api_reload_settings():
    """Manually reload settings from .env file."""
    global settings  # noqa: PLW0603
    try:
        from config.settings import reload_settings
        settings = reload_settings(require_api_keys=False)
        return JSONResponse({
            "status": "ok",
            "message": "Settings reloaded from .env successfully.",
            "wan21_enabled": settings.wan21_enabled,
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/settings/current")
async def api_settings_current():
    """Return current settings (safe subset) as JSON."""
    return JSONResponse({
        "video_provider_order": list(settings.video_provider_order),
        "minimax_enabled": settings.minimax_enabled,
        "wan21_enabled": settings.wan21_enabled,
        "runway_enabled": settings.runway_enabled,
        "minimax_unit_price": settings.minimax_unit_price,
        "image_provider": settings.image_provider,
        "midjourney_enabled": settings.midjourney_enabled,
    })


# ─── Playground ───────────────────────────────────────────────────────────────

import hashlib
import json as _json
import logging as _logging
import traceback as _tb

_pg_log = _logging.getLogger("playground")
PLAYGROUND_DIR = OUTPUT_DIR / "playground"
PLAYGROUND_DIR.mkdir(parents=True, exist_ok=True)


def _pg_save_meta(test_id: str, provider: str, category: str, prompt: str,
                  asset_file: str, cost: float, extra: dict | None = None) -> Path:
    """Save a JSON manifest alongside a playground asset for history browsing."""
    meta = {
        "id": test_id,
        "provider": provider,
        "category": category,
        "prompt": prompt,
        "asset_file": asset_file,
        "media_url": _file_to_media_url(asset_file),
        "cost": cost,
        "file_size_kb": round(Path(asset_file).stat().st_size / 1024, 1) if Path(asset_file).exists() else 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    meta_path = Path(asset_file).with_suffix(".json")
    meta_path.write_text(_json.dumps(meta, indent=2), encoding="utf-8")
    return meta_path


def _pg_test_id() -> str:
    return f"{int(datetime.now().timestamp())}_{hashlib.md5(os.urandom(8)).hexdigest()[:6]}"


@app.get("/playground", response_class=HTMLResponse)
async def playground_page(request: Request):
    el_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    el_vid = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    return templates.TemplateResponse("playground.html", {
        "request": request,
        "settings": settings,
        "elevenlabs_ready": bool(el_key and el_vid),
    })


@app.get("/api/playground/history")
async def api_playground_history(request: Request):
    """List saved playground results with pagination and optional provider filter."""
    provider_filter = request.query_params.get("provider", "").strip().lower()
    category_filter = request.query_params.get("category", "").strip().lower()
    page = max(1, int(request.query_params.get("page", 1)))
    per_page = min(50, max(5, int(request.query_params.get("per_page", 12))))

    meta_files = sorted(PLAYGROUND_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    items: list[dict] = []
    for mf in meta_files:
        try:
            data = _json.loads(mf.read_text(encoding="utf-8"))
            if provider_filter and data.get("provider", "").lower() != provider_filter:
                continue
            if category_filter and data.get("category", "").lower() != category_filter:
                continue
            asset_path = Path(data.get("asset_file", ""))
            data["media_url"] = _file_to_media_url(str(asset_path)) if asset_path.exists() else None
            data["file_exists"] = asset_path.exists()
            items.append(data)
        except Exception:
            continue

    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page
    page_items = items[start:start + per_page]

    total_cost = sum(i.get("cost", 0) for i in items)

    return JSONResponse({
        "items": page_items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "total_cost": round(total_cost, 4),
    })


@app.delete("/api/playground/{test_id}")
async def api_playground_delete(test_id: str):
    """Delete a single playground result by its test ID."""
    found = False
    for mf in PLAYGROUND_DIR.glob("*.json"):
        try:
            data = _json.loads(mf.read_text(encoding="utf-8"))
            if data.get("id") != test_id:
                continue
            found = True
            asset = Path(data.get("asset_file", ""))
            if asset.exists():
                asset.unlink()
            mf.unlink()
            # Ken Burns may have a corresponding input image
            kb_input = PLAYGROUND_DIR / f"kb_input_{test_id.split('_')[0]}.png"
            if kb_input.exists():
                kb_input.unlink()
            break
        except Exception:
            continue
    if not found:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"status": "deleted", "id": test_id})


@app.delete("/api/playground/clear/all")
async def api_playground_clear_all():
    """Delete all playground results."""
    count = 0
    for f in PLAYGROUND_DIR.iterdir():
        if f.is_file():
            f.unlink()
            count += 1
    return JSONResponse({"status": "cleared", "files_removed": count})


@app.post("/api/playground/dalle")
async def api_playground_dalle(request: Request):
    """Test DALL-E 3 image generation with a custom prompt."""
    try:
        body = await request.json()
        prompt = (body.get("prompt") or "").strip()
        size = body.get("size", "1792x1024")
        quality = body.get("quality", "hd")
        if not prompt:
            return JSONResponse({"error": "Prompt is required"}, status_code=400)
        if not settings.openai_api_key:
            return JSONResponse({"error": "OpenAI API key not configured"}, status_code=400)

        from openai import OpenAI
        import requests as req
        from PIL import Image
        from io import BytesIO

        tid = _pg_test_id()
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.images.generate(
            model="dall-e-3", prompt=prompt[:4000],
            size=size, quality=quality, n=1,
        )
        image_url = response.data[0].url
        img_data = req.get(image_url, timeout=60)
        img_data.raise_for_status()

        img = Image.open(BytesIO(img_data.content)).convert("RGB")
        fname = f"dalle_{tid}.png"
        out_path = PLAYGROUND_DIR / fname
        img.save(str(out_path), "PNG", optimize=True)

        cost = 0.12 if quality == "hd" else 0.08
        _pg_save_meta(tid, "dall-e", "image", prompt, str(out_path), cost,
                      {"size": size, "quality": quality})

        return JSONResponse({
            "id": tid,
            "image_url": _file_to_media_url(str(out_path)),
            "cost": cost,
            "size": size,
            "quality": quality,
        })
    except Exception as exc:
        _pg_log.exception("Playground DALL-E failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/playground/midjourney")
async def api_playground_midjourney(request: Request):
    """Test Midjourney image generation via GoAPI proxy."""
    try:
        body = await request.json()
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return JSONResponse({"error": "Prompt is required"}, status_code=400)
        if not settings.midjourney_api_key:
            return JSONResponse({"error": "Midjourney API key not configured"}, status_code=400)

        from pipeline.image_generator import _generate_midjourney_image
        tid = _pg_test_id()
        fname = f"mj_{tid}.png"
        out_path = PLAYGROUND_DIR / fname
        _generate_midjourney_image(prompt, out_path)

        _pg_save_meta(tid, "midjourney", "image", prompt, str(out_path), 0.05)

        return JSONResponse({
            "id": tid,
            "image_url": _file_to_media_url(str(out_path)),
            "cost": 0.05,
        })
    except Exception as exc:
        _pg_log.exception("Playground Midjourney failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/playground/video")
async def api_playground_video(request: Request):
    """Test any video provider (kling, minimax, runway) with a custom prompt."""
    try:
        body = await request.json()
        prompt = (body.get("prompt") or "").strip()
        provider = (body.get("provider") or "").strip().lower()
        duration = int(body.get("duration", 5))
        if not prompt:
            return JSONResponse({"error": "Prompt is required"}, status_code=400)
        if provider not in ("kling", "minimax", "runway"):
            return JSONResponse({"error": "provider must be kling, minimax, or runway"}, status_code=400)

        from pipeline.video_generator import VideoGenerator, ProviderError
        gen = VideoGenerator()
        prov = gen.providers.get(provider)
        if not prov or not prov.is_enabled():
            return JSONResponse({"error": f"{provider} is not enabled or configured"}, status_code=400)

        tid = _pg_test_id()
        fname = f"{provider}_{tid}.mp4"
        out_path = PLAYGROUND_DIR / fname
        prov.generate_clip(prompt=prompt, output_path=out_path, duration_seconds=duration)

        cost = 0.0
        if provider == "minimax":
            cost = settings.minimax_unit_price
        elif provider == "kling":
            from pipeline.cost_tracker import PRICING
            kling_model = settings.kling_model
            mode = "professional" if "pro" in kling_model.lower() else "standard"
            cost = PRICING["kling"].get(f"{mode}_{duration}s", 0.14)
        elif provider == "runway":
            cost = duration * 0.05

        file_size_mb = out_path.stat().st_size / (1024 * 1024) if out_path.exists() else 0

        _pg_save_meta(tid, provider, "video", prompt, str(out_path), cost,
                      {"duration": duration, "file_size_mb": round(file_size_mb, 2)})

        return JSONResponse({
            "id": tid,
            "video_url": _file_to_media_url(str(out_path)),
            "cost": cost,
            "provider": provider,
            "duration": duration,
            "file_size_mb": round(file_size_mb, 2),
        })
    except Exception as exc:
        _pg_log.exception("Playground video generation failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/playground/elevenlabs")
async def api_playground_elevenlabs(request: Request):
    """Test ElevenLabs text-to-speech with custom voice/settings."""
    try:
        body = await request.json()
        text = (body.get("text") or "").strip()
        voice_id = (body.get("voice_id") or "").strip()
        stability = float(body.get("stability", 0.4))
        similarity = float(body.get("similarity", 0.75))
        if not text:
            return JSONResponse({"error": "Text is required"}, status_code=400)

        api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        vid = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
        if not api_key or not vid:
            return JSONResponse({"error": "ElevenLabs not configured"}, status_code=400)

        import requests as req
        tid = _pg_test_id()
        model_id = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip()
        resp = req.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
            headers={"xi-api-key": api_key, "Accept": "audio/mpeg", "Content-Type": "application/json"},
            json={
                "text": text[:1000],
                "model_id": model_id,
                "voice_settings": {"stability": stability, "similarity_boost": similarity},
            },
            timeout=60,
        )
        resp.raise_for_status()

        fname = f"voice_{tid}.mp3"
        out_path = PLAYGROUND_DIR / fname
        out_path.write_bytes(resp.content)

        char_count = len(text[:1000])
        cost = round(char_count * 0.00003, 4)

        _pg_save_meta(tid, "elevenlabs", "audio", text[:1000], str(out_path), cost,
                      {"voice_id": vid, "stability": stability, "similarity": similarity,
                       "characters": char_count, "size_kb": len(resp.content) // 1024})

        return JSONResponse({
            "id": tid,
            "audio_url": _file_to_media_url(str(out_path)),
            "cost": cost,
            "size_kb": len(resp.content) // 1024,
            "characters": char_count,
        })
    except Exception as exc:
        _pg_log.exception("Playground ElevenLabs failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/playground/gpt")
async def api_playground_gpt(request: Request):
    """Test GPT-4o text generation with custom system/user prompts."""
    try:
        body = await request.json()
        system = (body.get("system") or "").strip()
        user = (body.get("user") or "").strip()
        model = body.get("model", "gpt-4o")
        max_tokens = int(body.get("max_tokens", 1000))
        temperature = float(body.get("temperature", 0.8))
        if not user:
            return JSONResponse({"error": "User prompt is required"}, status_code=400)
        if not settings.openai_api_key:
            return JSONResponse({"error": "OpenAI API key not configured"}, status_code=400)

        from openai import OpenAI
        tid = _pg_test_id()
        client = OpenAI(api_key=settings.openai_api_key)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        response = client.chat.completions.create(
            model=model, messages=messages,
            max_tokens=max_tokens, temperature=temperature,
        )
        text = response.choices[0].message.content
        usage = response.usage

        prompt_cost = (usage.prompt_tokens / 1_000_000) * (2.50 if model == "gpt-4o" else 0.15)
        completion_cost = (usage.completion_tokens / 1_000_000) * (10.00 if model == "gpt-4o" else 0.60)
        cost = round(prompt_cost + completion_cost, 6)

        # Save GPT output to a text file for browsing
        txt_path = PLAYGROUND_DIR / f"gpt_{tid}.txt"
        txt_path.write_text(text, encoding="utf-8")
        _pg_save_meta(tid, "gpt", "text", user, str(txt_path), cost,
                      {"system_prompt": system, "model": model, "temperature": temperature,
                       "usage": {"prompt_tokens": usage.prompt_tokens,
                                 "completion_tokens": usage.completion_tokens,
                                 "total_tokens": usage.total_tokens},
                       "output_text": text[:2000]})

        return JSONResponse({
            "id": tid,
            "text": text,
            "cost": cost,
            "model": model,
            "usage": {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            },
        })
    except Exception as exc:
        _pg_log.exception("Playground GPT failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/playground/kenburns")
async def api_playground_kenburns(request: Request):
    """Test Ken Burns pan/zoom effect on an uploaded image."""
    try:
        form = await request.form()
        image_file = form.get("image")
        effect = form.get("effect", "zoom_in")
        duration = int(form.get("duration", 5))

        if not image_file or not hasattr(image_file, "read"):
            return JSONResponse({"error": "Image file is required"}, status_code=400)

        tid = _pg_test_id()
        img_bytes = await image_file.read()
        img_path = PLAYGROUND_DIR / f"kb_input_{tid}.png"
        img_path.write_bytes(img_bytes)

        video_path = PLAYGROUND_DIR / f"kb_output_{tid}.mp4"

        from pipeline.video_generator import apply_ken_burns
        apply_ken_burns(
            image_path=img_path,
            output_path=video_path,
            effect=effect,
            duration_seconds=duration,
        )

        file_size_mb = video_path.stat().st_size / (1024 * 1024) if video_path.exists() else 0

        _pg_save_meta(tid, "kenburns", "video", f"Ken Burns {effect} ({duration}s)",
                      str(video_path), 0,
                      {"effect": effect, "duration": duration,
                       "file_size_mb": round(file_size_mb, 2),
                       "input_image": str(img_path)})

        return JSONResponse({
            "id": tid,
            "video_url": _file_to_media_url(str(video_path)),
            "cost": 0,
            "effect": effect,
            "duration": duration,
            "file_size_mb": round(file_size_mb, 2),
        })
    except Exception as exc:
        _pg_log.exception("Playground Ken Burns failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
