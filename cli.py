"""CLI entrypoint for operational pipeline commands."""

from __future__ import annotations

import json
import sys

import click
from sqlalchemy import select

from config.settings import get_settings
from database.connection import session_scope
from database.models import Character, Episode, EpisodeStatus, JobLog, JobStatus, VideoJob


settings = get_settings()


@click.group()
def cli() -> None:
    """Spartacus automation command line interface."""


@cli.command("generate")
def generate_command() -> None:
    """Trigger generation of one episode."""
    from main import run_pipeline

    try:
        run_pipeline()
        click.echo("Episode generation finished.")
    except Exception as exc:
        click.echo(f"Episode generation failed: {exc}", err=True)
        sys.exit(1)


@cli.command("upload")
def upload_command() -> None:
    """Manually trigger upload of the next ready episode."""
    import os
    from pathlib import Path

    from database.connection import SessionLocal, log_job_step
    from database.models import EpisodeSEO, Short, StepStatus
    from pipeline.youtube_upload import YouTubeUploader

    if not os.getenv("YOUTUBE_CLIENT_SECRET_PATH", ""):
        click.echo("YOUTUBE_CLIENT_SECRET_PATH not set in .env. Cannot upload.", err=True)
        sys.exit(1)

    session = SessionLocal()
    try:
        episode = session.execute(
            select(Episode)
            .where(Episode.status == EpisodeStatus.READY)
            .order_by(Episode.episode_number.asc())
            .limit(1)
        ).scalar_one_or_none()
        if not episode:
            click.echo("No ready episodes to upload.")
            return

        job = session.execute(
            select(VideoJob).where(VideoJob.episode_id == episode.id).order_by(VideoJob.id.desc()).limit(1)
        ).scalar_one_or_none()
        if not job or not job.final_video_path:
            click.echo(f"Episode {episode.episode_number} has no completed job with final video.", err=True)
            sys.exit(1)

        seo = session.execute(
            select(EpisodeSEO).where(EpisodeSEO.episode_id == episode.id)
        ).scalar_one_or_none()

        uploader = YouTubeUploader()
        video_path = Path(job.final_video_path)
        thumb_path = Path(job.thumbnail_path) if job.thumbnail_path else None

        video_id = uploader.upload_video(
            session=session, job_id=job.id, episode=episode,
            video_path=video_path, thumbnail_path=thumb_path, seo=seo,
        )
        click.echo(f"Uploaded episode {episode.episode_number}: https://youtube.com/watch?v={video_id}")

        short = session.execute(
            select(Short).where(Short.episode_id == episode.id).limit(1)
        ).scalar_one_or_none()
        if short and short.file_path and Path(short.file_path).exists():
            short_vid = uploader.upload_short(
                session=session, job_id=job.id, episode=episode,
                short=short, seo=seo,
            )
            click.echo(f"Uploaded Short: https://youtube.com/shorts/{short_vid}")

        session.commit()
    except Exception as exc:
        session.rollback()
        click.echo(f"Upload failed: {exc}", err=True)
        sys.exit(1)
    finally:
        session.close()


@cli.command("status")
def status_command() -> None:
    """Show recent episodes and statuses."""
    with session_scope() as session:
        episodes = list(
            session.execute(select(Episode).order_by(Episode.created_at.desc()).limit(20)).scalars()
        )
        if not episodes:
            click.echo("No episodes found.")
            return
        click.echo(f"{'EP':>4} | {'Status':<12} | {'Title':<50} | {'Created'}")
        click.echo("-" * 90)
        for ep in episodes:
            created = ep.created_at.strftime("%Y-%m-%d %H:%M") if ep.created_at else "—"
            click.echo(f"{ep.episode_number:>4} | {ep.status.value:<12} | {ep.title[:50]:<50} | {created}")


@cli.command("characters")
def characters_command() -> None:
    """List all characters with aggregate stats."""
    with session_scope() as session:
        characters = list(session.execute(select(Character).order_by(Character.name.asc())).scalars())
        if not characters:
            click.echo("No characters found. Run database/seed.py first.")
            return
        click.echo(f"{'Name':<15} | {'Origin':<12} | {'Style':<18} | {'W':>3} | {'L':>3} | {'Alive':>5} | {'Voice'}")
        click.echo("-" * 95)
        for c in characters:
            alive = "Yes" if c.is_alive else "Dead"
            voice = c.voice_id[:12] + "…" if c.voice_id and len(c.voice_id) > 12 else (c.voice_id or "—")
            click.echo(
                f"{c.name:<15} | {c.origin:<12} | {c.fighting_style:<18} | "
                f"{c.wins:>3} | {c.losses:>3} | {alive:>5} | {voice}"
            )


@cli.command("jobs")
def jobs_command() -> None:
    """Show recent jobs."""
    with session_scope() as session:
        jobs = list(session.execute(select(VideoJob).order_by(VideoJob.created_at.desc()).limit(20)).scalars())
        if not jobs:
            click.echo("No jobs found.")
            return
        click.echo(f"{'ID':>5} | {'Episode':>7} | {'Status':<10} | {'Duration':>10} | {'Created'}")
        click.echo("-" * 70)
        for job in jobs:
            dur = f"{job.duration_seconds:.1f}s" if job.duration_seconds else "—"
            ep = str(job.episode_id) if job.episode_id else "—"
            created = job.created_at.strftime("%Y-%m-%d %H:%M") if job.created_at else "—"
            click.echo(f"{job.id:>5} | {ep:>7} | {job.status.value:<10} | {dur:>10} | {created}")


@cli.command("retry")
@click.option("--job-id", required=True, type=int, help="ID of the failed job to retry.")
def retry_command(job_id: int) -> None:
    """Retry a failed job from where it left off."""
    from database.connection import SessionLocal, log_job_step
    from database.models import StepStatus

    session = SessionLocal()
    try:
        job = session.execute(select(VideoJob).where(VideoJob.id == job_id)).scalar_one_or_none()
        if not job:
            click.echo(f"Job {job_id} not found.", err=True)
            sys.exit(1)
        if job.status != JobStatus.FAILED:
            click.echo(f"Job {job_id} is {job.status.value}, not failed. Use --force if needed.", err=True)
            sys.exit(1)

        job.status = JobStatus.RUNNING
        session.add(job)
        log_job_step(session, job.id, "retry", StepStatus.STARTED, f"Manual CLI retry for job {job_id}")
        session.commit()
        click.echo(f"Job {job_id} set to RUNNING. Triggering pipeline…")
    finally:
        session.close()

    from main import run_pipeline

    try:
        run_pipeline()
        click.echo(f"Job {job_id} retry completed successfully.")
    except Exception as exc:
        click.echo(f"Job {job_id} retry failed: {exc}", err=True)
        sys.exit(1)


@cli.command("schedule")
def schedule_command() -> None:
    """Show expected cron schedule."""
    click.echo("Celery Beat Schedule:")
    click.echo("  Daily  2:00 AM UTC → Generate new episode (if <3 ready)")
    click.echo("  Daily  9:00 AM UTC → Upload next ready episode to YouTube")
    click.echo("  Sunday 1:00 AM UTC → Batch generate 7 episodes for the week")
    click.echo("  Every  30 minutes   → Retry failed jobs (max 3 retries each)")


@cli.command("open-final-dir")
def open_final_dir_command() -> None:
    """Print final output directory path."""
    click.echo(str(settings.final_dir))


@cli.command("seed")
def seed_command() -> None:
    """Seed initial series and character data."""
    from database.seed import seed_initial_data

    seed_initial_data()
    click.echo("Seed complete.")


@cli.command("costs")
@click.option("--episode-id", type=int, default=None, help="Episode ID to show costs for.")
def costs_command(episode_id: int | None) -> None:
    """Show API cost summary."""
    from pipeline.cost_tracker import get_episode_costs

    with session_scope() as session:
        if episode_id:
            costs = get_episode_costs(session, episode_id)
            click.echo(json.dumps(costs, indent=2, default=str))
        else:
            from database.models import ApiCostLog
            from sqlalchemy import func

            rows = session.execute(
                select(
                    ApiCostLog.service,
                    func.sum(ApiCostLog.cost_usd).label("total"),
                    func.count().label("count"),
                ).group_by(ApiCostLog.service)
            ).all()
            if not rows:
                click.echo("No cost data recorded yet.")
                return
            click.echo(f"{'Service':<15} | {'Total USD':>10} | {'Calls':>6}")
            click.echo("-" * 40)
            grand_total = 0.0
            for row in rows:
                total = float(row.total or 0)
                grand_total += total
                click.echo(f"{row.service:<15} | ${total:>9.4f} | {row.count:>6}")
            click.echo("-" * 40)
            click.echo(f"{'TOTAL':<15} | ${grand_total:>9.4f}")


if __name__ == "__main__":
    cli()
