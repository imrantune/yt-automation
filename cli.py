"""CLI entrypoint for operational pipeline commands."""

from __future__ import annotations

import json
import sys

import click
from sqlalchemy import select

from config.settings import get_settings
from database.connection import session_scope
from database.models import Character, Episode, VideoJob


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


@cli.command("status")
def status_command() -> None:
    """Show recent episodes and statuses."""
    with session_scope() as session:
        episodes = list(
            session.execute(select(Episode).order_by(Episode.created_at.desc()).limit(20)).scalars()
        )
        payload = [
            {
                "episode_number": ep.episode_number,
                "title": ep.title,
                "status": ep.status.value,
                "created_at": ep.created_at.isoformat() if ep.created_at else None,
            }
            for ep in episodes
        ]
        click.echo(json.dumps(payload, indent=2))


@cli.command("characters")
def characters_command() -> None:
    """List all characters with aggregate stats."""
    with session_scope() as session:
        characters = list(session.execute(select(Character).order_by(Character.name.asc())).scalars())
        payload = [
            {
                "name": c.name,
                "origin": c.origin,
                "style": c.fighting_style,
                "wins": c.wins,
                "losses": c.losses,
                "is_alive": c.is_alive,
            }
            for c in characters
        ]
        click.echo(json.dumps(payload, indent=2))


@cli.command("jobs")
def jobs_command() -> None:
    """Show recent jobs."""
    with session_scope() as session:
        jobs = list(session.execute(select(VideoJob).order_by(VideoJob.created_at.desc()).limit(20)).scalars())
        payload = [
            {
                "id": job.id,
                "episode_id": job.episode_id,
                "status": job.status.value,
                "final_video_path": job.final_video_path,
            }
            for job in jobs
        ]
        click.echo(json.dumps(payload, indent=2))


@cli.command("schedule")
def schedule_command() -> None:
    """Show expected cron schedule."""
    click.echo("Daily 2AM UTC -> generate new episode")
    click.echo("Sunday 1AM UTC -> generate 7-episode weekly batch")


@cli.command("open-final-dir")
def open_final_dir_command() -> None:
    """Print final output directory path."""
    click.echo(str(settings.final_dir))


if __name__ == "__main__":
    cli()
